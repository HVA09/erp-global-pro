# ERP Global Pro — سجل الاستمرارية الرئيسي

**آخر تحديث:** 14 نوفمبر 2026 — Stage 22: PostgreSQL/Psycopg 3 live CI harness (لم يُشغّل على خادم فعلي بعد)
**مخطط قاعدة البيانات:** SQLite v8
**أحدث حزمة:** `erp-global-pro-stage22-postgres-live-ci.zip`
**نقطة الاستئناف التالية:** Stage 23 — تشغيل PostgreSQL live job في GitHub Actions وفحص نتائجه، ثم PostgreSQL migrations/composite FKs/RLS بعد نجاح الاختبار الفعلي.
**الغرض:** هذا الملف احتفظ بتفاصيل Stage 16 التاريخية. للحالة الحالية ونقطة الاستئناف المعتمدة استخدم `ERP_GLOBAL_PRO_CONTINUITY_AR.md`؛ لا تعتبر أوامر Stage 17 التالية أحدث خطة.

## 1. حالة Stage 16 التاريخية

- Stage 16 أضاف واجهة المرفقات الخاصة في شاشة المستندات، وفحصًا fail-closed، وحجر الملفات المصابة، وحصة التخزين وحد رفع لكل مستخدم/شركة، وحزمة نسخ احتياطي واستعادة متكاملة لقاعدة البيانات والملفات.
- مخطط SQLite وصل إلى **v8**: أعمدة `scan_status`, `scan_engine`, `scanned_at` في `file_objects`، وجدول `file_upload_buckets`.
- التنزيل مسموح فقط عندما تكون حالة الفحص `clean`. الحالات `pending`, `infected`, `error` محجوبة.
- أمر ClamAV: `python manage.py scan-files`; يمكن إعادة حالات الخطأ عبر `--retry-errors`.
- الإعدادات: `ERP_FILE_QUOTA_BYTES=262144000` (250 MiB لكل شركة افتراضيًا)، `ERP_FILE_UPLOADS_PER_HOUR=30`، `ERP_CLAMSCAN_BIN=clamscan`، و`ERP_MAINTENANCE_MODE=0`.
- أضيفت حزمة نسخ احتياطي واحدة لقاعدة SQLite والملفات: `backup-bundle`; استعادة تحت وضع الصيانة وبالخادم متوقف: `restore-bundle`; الاستعادة من journal متقطع: `recover-bundle-restore`.
- حزمة النسخ تشمل database snapshot وbytes الملفات النظيفة/المعلقة/المعزولة وmanifest وبصمات SHA-256. يتحقق restore من ZIP inventory ومساراته، integrity/FK في SQLite، إصدار v8، وسجلات file_objects مقابل bytes قبل التبديل، ورفض symlinks في الجذور والملفات وعناصر ZIP.
- قبل تبديل DB والملفات يتم staging وrestore journal؛ التطبيق يمنع بدء التشغيل إذا بقي marker. تحتفظ الاستعادة الناجحة بنسخ rollback القديمة. لا يوجد cleanup تلقائي لهذه النسخ.
- `audit-file-orphans` يصدر تقريرًا فقط ولا يحذف ملفات مجهولة. `check-files` يفحص سلامة المرفقات.
- مجموعة اختبارات `test_tenant*.py`: **155/155 PASS** بعد تقوية مسارات الملفات الرمزية. اختبارات حزمة النسخ والاستعادة: **16/16**، واختبارات مخزن الملفات: **28/28**. Python compileall PASS؛ JavaScript syntax 19/19 PASS.
- Flask غير مثبت في بيئة الفحص؛ لم تُشغل اختبارات HTTP/Flask أو E2E حقيقية. اختبارات API/الخدمات السابقة تستخدم SQLite وFlask stubs حيث يلزم.
- ClamAV غير مثبت في بيئة البناء؛ اختُبرت حالة clean/infected باستخدام ماسح وهمي، ولم يتم فحص ملفات فعلية بمضاد فيروسات.
- PostgreSQL وcomposite foreign keys وRLS لم تنفذ. لا اختبارات pool/session leakage أو load/concurrency بعد.
- `TENANT_DATA_SCOPING_ENABLED=False` ويجب أن تبقى كذلك. لا تسمح بتشغيل شركات حقيقية متعددة بعد.
- لا اتصال بقاعدة إنتاج، لا نشر، لا إنشاء أو push إلى GitHub.

**الخلاصة:** Stage 16 أكمل طبقة الملفات الخاصة وواجهة إدارتها وفحصها والحجر والحصص والنسخ الاحتياطي والاستعادة المشتركة للـDB والملفات، مع تحقق hashes ورفض symlink paths. ما زال النظام تطويرًا وليس ERP متعدد الشركات معتمدًا للإنتاج.

## 2. قواعد المشروع الثابتة

1. ERP Global Pro مستقل تمامًا عن `HVA09/ai-chat-app` و`HVA09/lumen-learning-platform`.
2. لا ترفع ملفات ERP إلى مستودعي AI Chat App أو Lumen.
3. الأرشيف الأصلي لا يُعدل؛ كل التطوير في نسخة عمل مستقلة.
4. الهدف منصة واحدة متعددة الشركات مع عزل كامل، لكل القطاعات عبر industry packs تدريجية.
5. هوية المستخدم عالمية (`users`)، أما العضوية والدور الفعلي فمرتبطان بالشركة (`company_memberships`).
6. لا تثق في `company_id` المرسل من المتصفح؛ اشتقه من سياق JWT + العضوية على الخادم.
7. كل قراءة وكتابة وتقارير واستيراد/تصدير وملفات ومهام مستقبلية يجب أن تحمل سياق الشركة.
8. لا تشغيل متعدد الشركات قبل اجتياز اختبارات الخدمات وHTTP وقاعدة البيانات والنسخ الاحتياطي.
9. لا تعِد كتابة Flask/JavaScript دون سبب قابل للقياس وخطة ترحيل واختبارات.
10. التكلفة صفر قدر الإمكان أثناء التطوير.
11. ZIP backup ليس مشفرًا ولا موقّعًا: احفظه في storage خاص ومشفر على مستوى الوسيط. SHA-256 يتحقق من سلامة البايتات ولا يثبت المصدر.

## 3. سجل المراحل

| المرحلة | الإنجاز | الحالة |
|---|---|---|
| Architecture | معمارية multi-tenant والقرارات الأمنية وخطة التنفيذ | مرجع التصميم |
| Stage 1 | `companies` و`company_memberships` وترحيل الهوية | أساس الهوية |
| Stage 2 | الشركة النشطة والتحقق من العضوية وتبديل الشركة | سياق موثوق |
| Stage 3 | `company_id` لجداول الأعمال وحواجز الكتابة | أعيد بناؤها من Stage 2 |
| Stage 4 | 55 علاقة و110 trigger guards وفهارس مساعدة | Reconciled؛ ليست بديلًا عن PostgreSQL composite FKs |
| Stage 5 | preflight لـ15 قاعدة تفرد | قراءة فقط |
| Stage 6 | تدقيق ساكن لـCRUD وAPI | كشف فجوات الاستعلامات |
| Stage 7 | عزل CRUD العام | 21 موردًا |
| Stage 8 | تدقيق callbacks والآثار الجانبية | كشف فجوات المخزون/المحاسبة |
| Stage 9 | خدمات المخزون والمحاسبة الأساسية والإعدادات | عزل جزئي متقدم |
| Stage 10 | SQLite v6: التسلسلات وقيود التفرد حسب الشركة | 15 قاعدة تفرد |
| Stage 11 | التجارة والمبيعات والمشتريات والمدفوعات | عزل خدمات التجارة |
| Stage 12 | الموارد البشرية والرواتب والتصنيع | عزل المسارات الأساسية |
| Stage 13 | CRM والصيانة وملخصات المشاريع | عزل المسارات المخصصة |
| Stage 14 | التقارير ولوحة التحكم والاستيراد/التصدير والتدقيق | 111 اختبارًا مركّزًا في تقرير المرحلة |
| Stage 15 | SQLite v7 ومخزن مرفقات خاص وواجهات API وفحص سلامة bytes | أساس الملفات الخاص |
| **Stage 16** | **SQLite v8، UI للمرفقات، ClamAV wrapper/quarantine، quota/rate limits، DB+files backup/restore journal** | **مكتملة حسب اختبارات المرحلة** |
| Stage 17–18 | runtime preflight وHTTP/CI gates | HTTP live معلّق بسبب التبعيات سابقًا؛ CI gate أضيف |
| Stage 19–20 | تدقيق PostgreSQL وعقد DatabaseAdapter/SQLiteAdapter | Prototype فقط |
| Stage 21 | Psycopg 3 adapter spike ومقارنة SQLAlchemy | Fake DB-API tests 48/48 مع العقد والـpreflight |
| **Stage 22** | **PostgreSQL live integration suite + PostgreSQL 16 CI service + RLS/pool reuse tests** | **مضافة ولم تُشغّل فعليًا حتى الآن** |

**ملاحظة استمرارية Stage 4:** الأرشيف القديم نفسه لم يكن متاحًا في جلسة الاسترجاع السابقة؛ أُعيد توفيق المرحلة في نسخة عمل مستقلة باسم Reconciled، ويجب عدم الادعاء بأن ZIP القديم استعيد حرفيًا.

## 4. تفاصيل Stage 16

### 4.1 المرفقات وواجهة الاستخدام

- `backend/erp/services/file_storage.py`: إدارة metadata وbytes والحصص وفحص AV والحجر؛ يرفض symlinks في التخزين والجذر الخاص بالشركة والحجر.
- `backend/erp/api/file_objects.py`: `GET/POST /api/file-objects`, `GET /api/file-objects/usage`, get/download/delete حسب الصلاحيات وسياق الشركة.
- `frontend/js/pages/ops.js`: قسم المرفقات الخاصة في شاشة المستندات. سجل الروابط القديم بقي منفصلًا.
- الأنواع PDF/PNG/JPG/JPEG/WEBP/TXT/CSV؛ حد 10 MiB لكل ملف؛ تخزين خارج frontend وبمفاتيح UUID. حصة افتراضية 250 MiB لكل شركة.
- تنزيل الملف لا يتم حتى تكون الحالة `clean`.

### 4.2 ClamAV والحجر

- الأمر `python manage.py scan-files [--retry-errors]`.
- `clamscan` لا يستدعى عبر shell؛ إذا لم يتوفر، لا تتغير الحالة إلى `clean`. يتحقق التطبيق من البصمة والحجم قبل وبعد الفحص، وعند التنزيل.
- إصابة الملف تنقل bytes إلى `.quarantine/<company_id>/` وتصبح الحالة `infected`. إذا كان مسار الحجر symlink أو غير آمن تتحول الحالة إلى `error` ويبقى التنزيل محجوبًا.
- `pending`, `infected`, `error` محجوبة من التنزيل.
- بيئة البناء الحالية ليس بها ClamAV؛ نتائج الماسح الحقيقي لا تزال غير مختبرة.

### 4.3 النسخ الاحتياطي والاستعادة

الأوامر من مجلد `backend`:

```bash
python manage.py backup-bundle /secure/backups/erp-full.zip
ERP_MAINTENANCE_MODE=1 python manage.py restore-bundle /secure/backups/erp-full.zip
python manage.py recover-bundle-restore
python manage.py check-files
python manage.py audit-file-orphans /secure/reports/orphans.json
```

- backup يستخدم SQLite online backup لإنشاء snapshot، ثم يضم كل bytes المشار إليها في `file_objects` بما فيها المعزول والمعلق، ويقارن size وSHA-256.
- restore يرفض ZIP غير الآمن/المتغير، ويفحص قاعدة البيانات ومطابقة manifest والملفات قبل التبديل.
- `restore-bundle` يتطلب إيقاف كل web workers والمهام الخلفية، تفعيل `ERP_MAINTENANCE_MODE=1`، وأن يكون مجلد DB وملفات التخزين على filesystem واحد.
- إذا بقي `.restore-in-progress.json` يمنع التطبيق startup حتى `recover-bundle-restore`.
- بعد restore ناجح تُحفظ نسخ rollback `.pre-restore-<id>` ولا يوجد cleanup تلقائي لها.
- حزمة ZIP غير مشفرة وغير موقعة؛ يجب حمايتها وتشفير وسيط تخزين النسخ.

### 4.4 فحص الملفات اليتيمة

- `audit-file-orphans` يكتشف unknown files / symlinks ويصدر تقريرًا فقط؛ `deleted=0` ولا يحذف تلقائيًا.
- `check-files` يتحقق من حجم الملف وبصمة SHA-256 ومكانه.

### 4.5 cache والمهام والتنبيهات

- cache إعدادات الشركة والحسابات موجود في `flask.g` لكل request وليس process-global.
- `_cols_cache` في `db.py` يحتفظ بمعلومات schema/columns فقط.
- لا يوجد background queue أو scheduler أو notification engine مستقل في المشروع. أوامر CLI لفحص الملفات لا تمثل worker مجدولًا. عند إضافة worker مستقبلًا يجب تمرير `company_id` والتحقق منه في كل service.

## 5. التحقق في Stage 16

- `python -m unittest discover -s backend/tests -p 'test_tenant*.py' -v`: **155/155 PASS**.
- `backend/tests/test_tenant_stage16_backup_bundle.py`: **16/16 PASS**.
- `backend/tests/test_tenant_stage15_file_storage.py`: **28/28 PASS**.
- `python -m compileall -q backend`: PASS.
- `node --check` لكل ملفات frontend JavaScript: 19/19 PASS.
- تم اختبار v8 migration، الحصص، rate limit، الماسح الوهمي clean/infected، backup bundle، restore clean/pending/infected, tampering rejection, old rollback copies, restore journal recovery, unsafe path rejection.
- لم يتم اختبار HTTP الحقيقي أو متصفح حقيقي بسبب غياب Flask/Browser runtime في بيئة البناء.
- لم يتم تشغيل ClamAV حقيقي لأنه غير مثبت.

التفاصيل في `docs/ERP_STAGE_16_VALIDATION.txt` و`docs/ERP_STAGE_16_PRIVATE_FILES_BACKUP_CHANGELOG_AR.md` و`docs/ERP_STAGE_16_PRIVATE_FILES_BACKUP_RUNBOOK_AR.md`.

## 6. خطة Stage 17 التاريخية وقت Stage 16

1. تجهيز بيئة اختبار كاملة بتبعيات المشروع وتشغيل Flask HTTP integration tests حقيقية؛ لا تعد stubs بديلًا عن ذلك.
2. اختبار تسجيل الدخول واختيار الشركة وصلاحيات `documents.*` وupload/download/delete لشركتين مع IDOR وملفات تحمل الاسم نفسه.
3. اختبار maintenance mode وstartup guard عند وجود restore journal عبر تشغيل تطبيق فعلي.
4. تشغيل ClamAV حقيقي على بيئة خادم مع تحديث signature database واختبار عينات اختبار آمنة.
5. تنفيذ backup/restore لقاعدة التطبيق كاملة مع ملفات فعلية، وحقن أعطال في كل نقطة تبديل للتحقق من rollback/recovery.
6. اختبار واجهة browser E2E وإمكانية الوصول والتجربة على الشاشات الصغيرة.
7. بعد سد فجوات الاختبار، البدء في PostgreSQL migration وcomposite FK + RLS، وليس قبل ذلك.

## 7. شروط إغلاق تعدد الشركات

لا تغيّر `TENANT_DATA_SCOPING_ENABLED` حتى:

- كل جداول الأعمال ومراجعها ومرفقاتها معزولة.
- كل APIs/reports/import/export/jobs/files/backup/restore scoped.
- اختبارات شركتين متزامنتين بأسماء وSKU وأكواد وأرقام مستندات متكررة.
- اختبارات IDOR وforeign FK وcallback وسياق worker.
- HTTP auth حقيقي واختبارات سلبية للصلاحيات.
- PostgreSQL composite foreign keys وRLS وpool/session leakage.
- backup/restore مستقل لشركة واستعادة platform كاملة بما فيها bytes.
- malware scanning، quotas وorphan recovery.

## 8. GitHub والتسليم

- المستودع المقترح: `HVA09/erp-global-pro`، لم يتم إنشاؤه بعد.
- لا تضع ERP داخل AI Chat App أو Lumen.
- التسليم النهائي بعد Release Gate سيكون ZIP كاملًا: الكود، الاختبارات، الوثائق، README، `.env.example`، دليل التشغيل والنشر، وترحيلات DB + files backup/restore مع SHA-256.
- لا تدّعِ رفع GitHub أو النشر ما لم ينفذ ويُتحقق منه فعليًا.

## 9. بصمة الأرشيف الأصلي

`7943d5f5b1f027463a9ef2bd65116286ad76abdcde7db2e90bcedb4434e9895b`


## 10. Stage 22 — PostgreSQL live CI (14 نوفمبر 2026)

- `backend/tests/test_postgres_psycopg_adapter_integration.py`: 11 اختبارًا حيًا تستخدم DSN إداريًا لقاعدة disposable، وتنشئ role محدودًا ومخططًا عشوائيًا، وتجرب `FORCE ROW LEVEL SECURITY` مع شركتين.
- `backend/scripts/require_postgres_integration.py`: فحص إلزامي للـDSN والـdriver والخادم.
- `backend/requirements-postgres.txt`: `psycopg[binary,pool]>=3.3,<4` اختياري.
- `.github/workflows/erp-backend-ci.yml`: job منفصل بخدمة PostgreSQL 16 وPython 3.11/3.12.
- يغطي الاختبار إعادة استخدام اتصال pool بحجم واحد والتأكد من عدم تسرب `app.company_id` بعد commit بين المستأجرين.
- محليًا لم تتوفر Psycopg/Pool أو PostgreSQL؛ live suite لم تُشغّل، وتشغيلها دون DSN ينتج skip صريحًا. CI يفرض `REQUIRE_POSTGRES_INTEGRATION=1` ويجب أن يفشل عند غياب البيئة.
- هذه سياسات RLS تجريبية على جدول عينة، وليست RLS لكل ERP. custom GUC سياق يحدده التطبيق بعد التحقق من membership، ولا يعالج SQL injection بذاته.
- SQLite v8 تظل backend الوحيدة المفعّلة. `TENANT_DATA_SCOPING_ENABLED=False`. لا PostgreSQL migrations كاملة أو composite FKs، لا رفع GitHub، لا نشر.

راجع `ERP_GLOBAL_PRO_CONTINUITY_AR.md` و`ERP_GLOBAL_PRO_STAGE22_CHANGELOG_AR.md` و`ERP_GLOBAL_PRO_STAGE22_RUNBOOK_AR.md` قبل أي تغيير تالٍ.
