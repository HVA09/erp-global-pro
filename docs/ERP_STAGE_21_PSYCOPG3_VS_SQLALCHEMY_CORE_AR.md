# ERP Global Pro — Stage 21: قرار PostgreSQL Driver

**التاريخ:** 14 نوفمبر 2026
**القرار:** اعتماد `psycopg 3` كمسار النموذج الأولي الأول، وعدم اعتماد SQLAlchemy Core حاليًا.
**النطاق:** prototype معزول؛ لا يوجد تفعيل داخل التطبيق ولا اتصال بخادم PostgreSQL.

## 1. ملخص القرار

الكود الحالي ليس مبنيًا على ORM؛ بل يستعمل واجهة SQLite/DB-API مباشرة عبر دوال `q`, `one`, `scalar`, `execute`, `run`، ويعتمد على:

- `connection.cursor()` و`execute()` و`executemany()`.
- معاملات صريحة `BEGIN` و`COMMIT` و`ROLLBACK` و`SAVEPOINT`.
- شكل الصف الذي يقبل `row[0]` و`row['column']`، كما يستعمل `dict(row)`.
- إرجاع رقم آخر سجل عبر `lastrowid` في SQLite.
- معاملات SQL موضعية بعلامة `?`.

لهذا فإن `psycopg 3` أقرب إلى الواجهة الحالية: توثيقه يذكر أن اتصال Psycopg يطبق DB-API، ويتيح Row Factories مخصصة. أما SQLAlchemy Core فيضيف `Engine` و`Connection` وDialect/Pooling وواجهات نتائج؛ وهو خيار قوي، لكنه لا يحول SQL الخام أو `?` تلقائيًا إلى صيغة محمولة.

## 2. المقارنة التنفيذية

| المعيار | Psycopg 3 مباشرة | SQLAlchemy Core |
|---|---|---|
| التوافق مع DB-API الموجود | مرتفع؛ الاتصال يطبّق DB-API | متوسط؛ يحتاج طبقة Engine/Connection أو `exec_driver_sql` |
| إبقاء SQL الخام | ممكن مع تكييف placeholder | ممكن عبر `exec_driver_sql`؛ أما `text()` فتستخدم bind parameters مسماة |
| شكل الصف | Row Factory مخصصة لتقليد `sqlite3.Row` | `Row` و`RowMapping`؛ يحتاج مواءمة `dict(row)`/الفهرسة مع الاستعمال الحالي |
| إدارة الاتصال | اتصال مباشر؛ pool إضافي عند الحاجة | Engine وPool مدمجان في نمط الاستخدام المعتاد |
| تكلفة إعادة الهيكلة المتوقعة | أقل مبدئيًا، لكن يلزم معالجة placeholders و`RETURNING` | أعلى إذا أردنا الاستفادة من Core فعليًا وإعادة صياغة SQL إلى expressions/named binds |
| ترحيلات المخطط | يجب بناؤها منفصلة | يمكن أن يساعد في تعريف المخطط/DDL، لكنه لا ينقل ترحيلات SQLite الحالية تلقائيًا |
| قرار هذه المرحلة | **مختار للنموذج الأولي** | مؤجل؛ يعاد تقييمه إن ظهر احتياج فعلي للـCore/DDL |

## 3. النموذج الأولي المنفذ

أضيفت الملفات:

- `backend/erp/db_adapters/postgres_psycopg.py`
- `backend/tests/test_postgres_psycopg_adapter_spike.py`
- `backend/requirements-postgres.txt` (اعتماد اختياري غير مضاف إلى `requirements.txt` العام)

### 3.1 ما يغطيه النموذج

1. `Psycopg3Adapter` يطابق بنيويًا `DatabaseAdapter`.
2. اتصال Psycopg lazy-import؛ لا يتطلب تثبيت psycopg لتشغيل SQLite أو استيراد الوحدة.
3. `HybridRow` يحافظ على الوصول الرقمي والاسمي و`dict(row)`.
4. مترجم محافظ يحول `?` إلى `%s` بعد تجاهل علامات الاستفهام داخل النصوص والاقتباسات والتعليقات وDollar-quoted strings.
5. يتحقق من عدد المعاملات، ويرفض التعبيرات الملتبسة مثل PostgreSQL JSON `?` operators بدل تخمين المقصود.
6. يضاعف `%` حرفيًا عندما توجد معاملات bound وفق صيغة Psycopg.
7. يدعم `executemany` بعد التحقق المسبق من اتساق عدد معاملات الصفوف.
8. يعيد رقم السجل فقط عندما يكتب الاستعلام صراحةً `INSERT ... RETURNING id`.
9. يقرأ أعمدة الجداول من `information_schema.columns`.
10. يرفض `executescript` عمدًا؛ يجب إنشاء ترحيلات PostgreSQL ذات إصدارات وخطوات مستقلة.
11. يرفض `begin(write=True)` عمدًا؛ PostgreSQL `BEGIN` لا يعادل حجز الكاتب الذي يقدمه SQLite `BEGIN IMMEDIATE`، ويجب تصميم أقفال/تزامن PostgreSQL قبل التوصيل.

### 3.2 ما لا يغطيه

- لم يجر الاتصال بخادم PostgreSQL؛ لا يوجد `psql`, `postgres`, `initdb`, Docker أو Podman في بيئة البناء.
- حزمة `psycopg` غير مثبتة؛ لذلك لم يتم اختبار DB-API مع السائق الحقيقي.
- لم تتم إضافة إعداد `DATABASE_URL` أو اختيار backend داخل `erp/db.py`.
- لم تُكتب migrations أو schema PostgreSQL.
- لم يتم تحويل استدعاءات `lastrowid` أو `INSERT OR IGNORE` أو `AUTOINCREMENT` أو `PRAGMA` أو triggers.
- لم يتم إعداد RLS أو مفاتيح أجنبية مركبة.
- مسار write transaction يرفض حاليًا `write=True` إلى أن يتم تصميم بديل صريح لدلالة `BEGIN IMMEDIATE` واختباره مع التزامن.

**هذه أداة prototype وليست PostgresAdapter إنتاجيًا. لا تغيّر عنوان قاعدة البيانات إلى PostgreSQL ولا تحاول تشغيل التطبيق بها.**

## 4. مخاطر معاملات SQL

### 4.1 تحويل `?`

Psycopg يستخدم `%s` للمعاملات الموضعية. المترجم في هذا النموذج محدود بقصد الأمان: يقرأ SQL character-by-character ويحسب `?` خارج النصوص والتعليقات. إذا اختلف العدد عن المعاملات، يرفع `PlaceholderMismatch`.

PostgreSQL يدعم JSON operators منها `?`, `?|`, و`?&`. أي query يحتويها قد يُرفض في النموذج الأولي؛ ينبغي إعادة كتابة تعبيرات JSON بواجهات مثل `jsonb_exists` أو استخدام استعلام PostgreSQL أصلي منفصل بعد تدقيقه.

لا تعتمد على هذا المترجم لمعالجة كل SQL في المشروع. قبل النقل، يجب تدقيق query inventory وتحويل الاستعلامات ذات الصيغ الخاصة يدويًا.

### 4.2 `lastrowid`

لا ينبغي محاولة اختراع `lastrowid` عام على PostgreSQL. أي insert يحتاج ID يجب أن يطلبه صراحةً باستخدام `RETURNING id`. المرحلة التالية يجب أن تعدل call sites التي تعتمد على معرف الإدراج، وتختبر أن updates/deletes لا تستهلك cursor rows.

### 4.3 معاملات ونطاق الشركة

RLS يحتاج تعيين سياق الشركة داخل transaction فقط (مثل `SET LOCAL`) بعد التحقق من العضوية. يجب إجراء اختبار اتصال pooled: طلب الشركة A ثم إعادة استخدام نفس الاتصال للشركة B، وإثبات أن سياق A لا يبقى. لم ينفذ ذلك في Stage 21.

## 5. نتائج الاختبارات

- PostgreSQL adapter spike: **32/32 PASS** (باستخدام Fake DB-API connection/cursor؛ لا يتضمن خادم PostgreSQL).
- SQLite adapter contract: **10/10 PASS**.
- PostgreSQL compatibility preflight: **6/6 PASS**.
- مجموع هذه الحزم الثلاث: **48/48 PASS**.
- Python compileall: يجب أن ينجح على جميع `backend`.
- JavaScript syntax: يجب أن يظل 19/19.
- لا توجد اختبارات PostgreSQL runtime في هذه المرحلة. كما أن write transaction path غير مدعوم عمدًا في prototype إلى حين تصميم بديل آمن لدلالة `BEGIN IMMEDIATE`.

## 6. Stage 22 المقترحة

1. إضافة خدمة PostgreSQL مؤقتة إلى GitHub Actions (service container) أو بيئة محلية قابلة للتخلص منها.
2. تثبيت `requirements-postgres.txt` في job منفصل فقط.
3. اختبار psycopg الحقيقي: اتصال، row factory، qmark binding، percent escaping، executemany، transactions/savepoints، `RETURNING id`، SQLSTATE/error mapping.
4. اختبار rollback بعد خطأ وإعادة استخدام الاتصال؛ منع بقاء transaction أو `company_id` في pool.
5. اختبار compatibility لكل query بعد إزالة SQLite-only syntax، وليس الاكتفاء بتقرير preflight.
6. إبقاء SQLite backend الحالي افتراضيًا، وإبقاء `TENANT_DATA_SCOPING_ENABLED=False`.

## 7. المراجع الرسمية

- [Psycopg 3 — Connection classes](https://www.psycopg.org/psycopg3/docs/api/connections.html): واجهة اتصال DB-API.
- [Psycopg 3 — Row factories](https://www.psycopg.org/psycopg3/docs/advanced/rows.html): Row Factory المخصصة.
- [Psycopg 3 — Passing parameters](https://www.psycopg.org/psycopg3/docs/basic/params.html): `%s` و`%%` عند تمرير معاملات.
- [SQLAlchemy 2.0 — Working with Engines and Connections](https://docs.sqlalchemy.org/en/20/core/connections.html): `text()`, `exec_driver_sql`, transactions وpooling.

---

**Release gate:** لا PostgreSQL production، لا RLS، لا شركات متعددة حقيقية، ولا رفع GitHub في هذه المرحلة.
