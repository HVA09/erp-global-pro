# ERP Global Pro — نظام ERP متكامل

باك إند **Flask + SQLite** وواجهة **JavaScript خالصة** (بدون npm أو أي خطوة بناء). عربي/إنجليزي مع دعم RTL ووضع داكن.

## التشغيل السريع

**ويندوز:** انقر مرتين على `start.bat`
**لينكس / ماك:** `bash start.sh`

ثم افتح: http://127.0.0.1:8000 — في أول مرة تظهر شاشة الإعداد (اسم الشركة + حساب المدير)، ويمكنك تفعيل **بيانات تجريبية** لاستكشاف النظام.

يدوياً:
```
cd backend
python -m venv .venv && source .venv/bin/activate     # ويندوز: .venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## الوحدات

| الوحدة | المحتوى |
|---|---|
| المبيعات والمشتريات | فواتير، إشعارات دائن/مدين، مدفوعات جزئية، مرتجعات، تطبيق الرصيد الدائن، حد ائتمان، كشوف حساب وأعمار الديون |
| نقاط البيع (POS) | مسح الباركود، سلة، دفع نقدي/بنكي، إيصال للطباعة |
| المخزون | مستودعات، متوسط التكلفة المتحرك، دفعات وصلاحية (FEFO)، تسويات، تحويلات، جرد، تقييم المخزون |
| المحاسبة | قيد مزدوج، دليل حسابات، ميزان مراجعة، قائمة دخل، ميزانية، تدفقات نقدية، ضريبة، أصول ثابتة وإهلاك، قفل الفترات، فحص سلامة (10 فحوصات) |
| الموارد البشرية | موظفون، حضور، إجازات، سلف، رواتب مرحّلة للدفتر |
| التصنيع | قوائم مواد (BOM) وأوامر إنتاج بتحميل التكلفة |
| CRM والمشاريع والصيانة | عملاء محتملون، فرص، حملات، مشاريع ومهام، جداول صيانة |
| التقارير | لوحة تحكم، مبيعات، ربحية، أفضل المنتجات والعملاء، توقع بسيط، اقتراح إعادة الطلب |
| البيانات | تصدير CSV/Excel (8 مجموعات) واستيراد (منتجات، عملاء، موردون) مع فحص تجريبي قبل الحفظ |
| الإدارة | مستخدمون، أدوار وصلاحيات دقيقة، سجل تدقيق، نسخ احتياطي |

## تصميم يضمن سلامة الأرقام

- كل المبالغ **أعداد صحيحة (سنتات)** — لا أخطاء كسور عشرية.
- دفتر اليومية **للإضافة فقط** (محميّ بمشغّلات قاعدة البيانات)؛ التصحيح يتم بقيد عكسي.
- لا يمكن الترحيل في فترة مقفلة؛ ترقيم المستندات بلا فجوات.
- كلمات المرور بـ scrypt، رموز JWT، تحديد محاولات الدخول، ترويسات CSP.

## البنية

```
backend/
  run.py  manage.py  requirements.txt
  erp/  schema.sql  crud.py  resources.py  seed.py  security.py ...
    services/   منطق الأعمال (محاسبة، مخزون، مبيعات، رواتب ...)
    api/        نقاط الـ REST
  tests/        اختبارات أساسية واختبارات عزل متعددة الشركات
frontend/       index.html, css/, js/core, js/pages, sw.js (PWA)
```

## الإعدادات (متغيرات بيئة)

| المتغير | الافتراضي |
|---|---|
| `ERP_ENV` | `development` (اجعلها `production` عند النشر) |
| `ERP_SECRET_KEY` | يُولَّد تلقائياً في التطوير؛ **إلزامي** في الإنتاج |
| `ERP_DB_PATH` | `backend/instance/erp.sqlite3` |
| `ERP_FILE_STORAGE_DIR` | `backend/instance/files` (outside the frontend/static root) |
| `ERP_TOKEN_TTL_HOURS` | `12` |
| `ERP_CORS_ORIGINS` | فارغ (نفس الأصل فقط) |
| `ERP_FILE_QUOTA_BYTES` | `262144000` (250 MiB لكل شركة) |
| `ERP_FILE_UPLOADS_PER_HOUR` | `30` لكل مستخدم/شركة |
| `ERP_CLAMSCAN_BIN` | `clamscan` |
| `ERP_MAINTENANCE_MODE` | `0`؛ فعّله إلى `1` أثناء الاستعادة بعد إيقاف الخادم |
| `HOST` / `PORT` | `127.0.0.1` / `8000` |

## الأوامر المساعدة

```
python manage.py create-admin     # إنشاء/إعادة تعيين مدير
python manage.py seed-demo        # بيانات تجريبية (قاعدة فارغة فقط)
python manage.py backup FILE      # نسخة منصة كاملة؛ تُشغّل بواسطة مشغل المنصة فقط
python manage.py restore FILE     # استعادة (أوقف الخادم أولاً)
python manage.py check            # فحوصات سلامة المحاسبة
python manage.py check-files      # التحقق من وجود المرفقات ومطابقة أحجامها وبصماتها
python manage.py scan-files       # فحص الملفات المعلقة باستخدام ClamAV
python manage.py backup-bundle FILE  # نسخة واحدة لقاعدة البيانات + المرفقات
ERP_MAINTENANCE_MODE=1 python manage.py restore-bundle FILE  # الاستعادة والخادم متوقف
python manage.py recover-bundle-restore  # استرجاع rollback بعد انقطاع restore
python manage.py audit-file-orphans [REPORT.json]  # تقرير فقط، دون حذف
python -m unittest discover -s tests -v    # الاختبارات
```

اختبارات PostgreSQL الحية لمحول Psycopg 3 وسياق RLS الموقّع (قاعدة مؤقتة فقط؛ راجع أدلة Stage 22 وStage 23):

```bash
python -m pip install -r requirements-postgres.txt
export ERP_TEST_POSTGRES_ADMIN_DSN='postgresql://postgres:password@127.0.0.1:5432/erp_stage22_test'
export REQUIRE_POSTGRES_INTEGRATION=1
python scripts/require_postgres_integration.py
python -m unittest tests.test_postgres_psycopg_adapter_integration tests.test_postgres_signed_tenant_context_integration -v
```

فحص التعارضات قبل تحويل مفاتيح التفرد إلى نطاق الشركة (قراءة فقط؛ نفّذه على نسخة مستعادة منفصلة):

```bash
python scripts/tenant_uniqueness_preflight.py /path/to/restored-copy.sqlite
```

راجع `docs/ERP_STAGE_5_PREFLIGHT_GUIDE_AR.md` قبل تشغيل الأداة. لا تستخدم ملف قاعدة الإنتاج الحي.

## حالة تطوير تعدد الشركات — Stage 24 (آخر نقطة عمل)

Stage 23 يعالج نقطة ثقة ظهرت في RLS prototype السابق: متغير PostgreSQL مخصص مثل `app.company_id` يمكن لدور التطبيق تعيينه، لذلك لا يثبت وحده هوية الشركة. أضيف نموذج مستقل لـHMAC-signed tenant claims، ودالة PostgreSQL `SECURITY DEFINER` تتحقق من التوقيع والمهلة ثم تعيد فحص حالة عضوية المستخدم والشركة ونشاط حساب المستخدم، وتمنع التطبيق من القراءة أو الكتابة المباشرة إلى جداول التحكم، وتتحقق من خصائص الأدوار وملكية المخططات والجداول. كما أضيف قالب SQL ودليل تهديدات؛ وفي Stage 24 تمت إضافة وظائف قراءة آمنة `SECURITY DEFINER` لقائمة شركات المستخدم والملف الحالي والعضوية الحالية، مع 18 اختبار تصميم SQL و19 اختبار PostgreSQL حيًا لمجموعة سياق RLS. هذه الإضافة **prototype فقط**؛ لم يتم ربطها بمصادقة Flask أو JWT الحالي، ولم تطبق على جداول ERP الفعلية.

نتائج الفحص المحلي في Stage 24: Stage 21/22 أساس **48/48 PASS**، وStage 23 signer + SQL design **30/30 PASS**، والإجمالي **78/78 PASS**. فحوص Python وJavaScript وYAML ناجحة. اختبارات PostgreSQL الحية الخاصة بـStage 22 (11) وStage 23/24 (19)، أي **30 اختبارًا**، **NOT RUN LOCALLY** بسبب غياب PostgreSQL وPsycopg/Docker/DSN. تم توسيع GitHub Actions لتشغيل مجموعتي الاختبار الحي بعد رفع المشروع إلى مستودع مستقل، لكن workflow لم يُنفّذ حتى الآن. لا تعتبر الاختبارات الحية ناجحة قبل ظهور نتيجة CI فعلية.

حد أمني إضافي: القالب يسحب كل صلاحيات `PUBLIC` ودور التطبيق على جداول companies/company_memberships/users، بما فيها SELECT. أضيفت في Stage 24 وظائف قراءة محدودة فقط (`list_my_companies`, `get_current_company_profile`, `get_current_membership`, و`current_user_id`)؛ لا توجد وظائف إدارة عضويات أو bootstrap لتسجيل الدخول بعد. يتحقق القالب أيضًا من posture الأدوار ومن ملكية مخططات الثقة والجداول. مقارنة HMAC تمر على جميع 32 بايت لتجنب الخروج المبكر، لكنها لا تثبت ضمان constant-time منخفض المستوى. كما أن HMAC لا يعالج اختراق عملية التطبيق أو تسريب مفتاح التوقيع، ولا يغني عن Composite FKs ومراجعة جميع المسارات.

**هذا إصدار تطوير واختبار وليس نظامًا متعدد الشركات جاهزًا للإنتاج.** أبقِ `TENANT_DATA_SCOPING_ENABLED=False` ولا تضف شركات تشغيلية حقيقية. لم تُفحص قاعدة إنتاج ولم يحدث نشر أو رفع GitHub. اقرأ `ERP_GLOBAL_PRO_CONTINUITY_AR.md` ووثائق Stage 19–23، خصوصًا `ERP_GLOBAL_PRO_STAGE23_RLS_THREAT_MODEL_AR.md` و`ERP_GLOBAL_PRO_STAGE23_RUNBOOK_AR.md` و`ERP_GLOBAL_PRO_STAGE24_RUNBOOK_AR.md`.

## المرفقات والنسخ الاحتياطي — Stage 16

- شاشة المستندات تعرض المرفقات الخاصة مع رفع/قائمة/تنزيل/حذف حسب الصلاحيات.
- التنزيل متاح فقط عند `scan_status=clean`. يتحقق التطبيق من الحجم وSHA-256 قبل/بعد الفحص وعند التنزيل. شغّل `python manage.py scan-files` على خادم يحتوي ClamAV؛ إذا لم يتوفر الماسح فتبقى الملفات غير قابلة للتنزيل.
- جذر التخزين ومجلدات الشركات والحجر ترفض symlinks. إذا تعذر إنشاء مسار حجر آمن، يبقى الملف محجوبًا ولا يتم اعتماد حالته `clean`.
- الأنواع المسموحة PDF وPNG وJPG/JPEG وWEBP وTXT وCSV، بحد أقصى 10 MiB لكل ملف؛ الحصة الافتراضية 250 MiB لكل شركة و30 رفعًا لكل مستخدم/شركة/ساعة.
- النسخ الاحتياطي المتكامل: `python manage.py backup-bundle FILE`. الاستعادة تتطلب إيقاف الخادم وتفعيل `ERP_MAINTENANCE_MODE=1` ثم `python manage.py restore-bundle FILE`. راجع `docs/ERP_STAGE_16_PRIVATE_FILES_BACKUP_RUNBOOK_AR.md` قبل الاستخدام.
- حزمة ZIP غير مشفرة وغير موقعة رقميًا. خزّنها في موقع خاص ومشفر على مستوى وسيط التخزين؛ SHA-256 لا يثبت أصالة مصدر النسخة.
- لا يحذف `audit-file-orphans` أي ملفات؛ يعرض تقريرًا فقط. وتُحتفظ بنسخ rollback بعد الاستعادة الناجحة حتى مراجعتها يدويًا.
- اختبارات Stage 16 المركزة: 155/155؛ مخزن المرفقات: 28/28؛ حزمة backup/restore: 16/16. لا تتضمن هذه الأرقام اختبارات HTTP حقيقية.

## النشر الإنتاجي

```
ERP_ENV=production ERP_SECRET_KEY='<سر طويل عشوائي>' gunicorn -w 2 -b 127.0.0.1:8000 run:app
```
ضعه خلف Nginx/Caddy مع **HTTPS**. أمر `manage.py backup` نسخة قاعدة بيانات فقط على مستوى المنصة، ويُرفض إذا كانت هناك مرفقات مسجلة حتى لا ينتج نسخة ناقصة؛ استخدم `backup-bundle` لنسخة DB + files المتكاملة. لا تمنح أوامر النسخ أو الاستعادة لمسؤول شركة مستأجرة، لأنها عمليات platform-level وتتطلب مشغل النظام.

## حدود يجب معرفتها

- **ZATCA:** يولّد رمز QR (المرحلة الأولى: TLV بـ 5 حقول). **لا** يتضمن التوقيع الرقمي ولا الربط/الاعتماد مع هيئة الزكاة (المرحلة الثانية).
- **التوقع** في التقارير متوسط معدل بيع بسيط وليس تعلّماً آلياً.
- **SQLite** مناسبة للشركات الصغيرة والمتوسطة؛ لحمل كبير متعدد المستخدمين فكّر بالانتقال إلى PostgreSQL.
- Flask غير مثبت في بيئة البناء؛ لم تُشغّل اختبارات HTTP/Flask أو E2E حقيقية. بعض اختبارات API تستخدم Flask-compatible stubs.
- ClamAV غير مثبت في بيئة البناء؛ نتائج scanner اختُبرت عبر executable وهمي فقط. يجب تثبيت ClamAV وتحديث signatures قبل السماح بتنزيل المرفقات على خادم فعلي.
- PostgreSQL وRLS غير منفذين. واجهة المتصفح لم تختبر عبر browser E2E فعلي.




## Stage 17 — HTTP readiness

- Run the read-only environment check: `cd backend && python scripts/runtime_preflight.py`.
- Run the dependency-free readiness checks: `cd backend && python -m unittest tests.test_stage17_http_readiness -v`.
- After installing `backend/requirements.txt`, the same test module also runs real Flask test-client scenarios for authentication, company switching, attachment IDOR, upload ownership, maintenance mode and interrupted-restore startup protection.
- In the current build environment Flask/Werkzeug are missing, so five HTTP scenarios are skipped. Do not treat them as passed.
- `TENANT_DATA_SCOPING_ENABLED` remains `False`; this project is not approved for multi-company production.

See `docs/ERP_STAGE_17_HTTP_READINESS_AR.md` and `ERP_GLOBAL_PRO_CONTINUITY_AR.md`.

## Stage 18 — real HTTP CI gate

A GitHub Actions workflow is available at `.github/workflows/erp-backend-ci.yml`. It installs the backend dependencies, runs the complete backend unittest suite, runs Flask HTTP integration tests with a no-skip gate, and checks Python/JavaScript syntax.

For local HTTP tests, from `backend/` run:

```bash
python -m pip install -r requirements.txt
python scripts/require_http_dependencies.py
python -m unittest discover -s tests -v
python scripts/run_http_tests.py
```

If Flask/Werkzeug cannot be installed, the real HTTP test gate must remain blocked; static checks are not a substitute. Multi-company production remains disabled until PostgreSQL/RLS and the full release gate pass.


## Stage 19–20 — PostgreSQL audit and database adapter contract

- Generate the read-only inventory: `cd backend && python scripts/postgres_compat_preflight.py --output artifacts/postgres-compat-preflight.json`.
- Run contract tests: `cd backend && python -m unittest tests.test_database_adapter_contract tests.test_postgres_compat_preflight -v`.
- `erp/db.py` delegates central connection/execute/transaction/introspection work to `SQLiteAdapter`; migrations, schema, backup/restore, and several admin paths remain SQLite-only.
- No PostgreSQL driver, adapter, migrations, composite foreign keys or RLS have been implemented. Do not configure PostgreSQL by changing a connection string.
- The question-mark inventory is lexical only and must not be mass-replaced.
- `TENANT_DATA_SCOPING_ENABLED=False` remains mandatory.

See `ERP_GLOBAL_PRO_CONTINUITY_AR.md`, `docs/ERP_STAGE_19_POSTGRES_AUDIT_AR.md`, and `docs/ERP_STAGE_20_DB_ADAPTER_CONTRACT_AR.md`.


## Stage 24 — وظائف قراءة آمنة لمخطط التحكم

قالب PostgreSQL يضيف وظائف `SECURITY DEFINER` محدودة بعد التحقق من سياق الشركة الموقع: `list_my_companies()` لعرض العضويات النشطة للمستخدم الحالي فقط، `get_current_company_profile()`، `get_current_membership()`، و`current_user_id()`. لا تمنح الوظائف وصول SELECT مباشرًا إلى دور التطبيق ولا تتضمن أي تعديل أو إدارة. هذه ليست وظائف تسجيل الدخول الأولي؛ تتطلب سياق شركة موقعًا صالحًا مسبقًا.

الاختبارات المركزة: **78/78 PASS**. اختبارات PostgreSQL الحية: 30 مهيأة ضمن CI (11 لمحول Psycopg و19 لسياق RLS وواجهات القراءة)، لكنها لم تُشغّل بعد. لا تعتبر Stage 24 اعتماد PostgreSQL أو RLS شاملًا. SQLite v8 تظل قاعدة التطبيق، وتشغيل تعدد الشركات ما زال محظورًا. راجع `ERP_GLOBAL_PRO_CONTINUITY_AR.md` و`ERP_GLOBAL_PRO_STAGE24_CHANGELOG_AR.md`.
