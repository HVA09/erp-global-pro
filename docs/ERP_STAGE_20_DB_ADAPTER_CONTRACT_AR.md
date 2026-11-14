# ERP Global Pro — Stage 20: عقد طبقة قاعدة البيانات

## ما تم

1. إضافة `backend/erp/db_adapters/protocol.py` لتعريف عقد adapter: connect/execute/executemany/executescript/begin/commit/rollback/in_transaction/last_insert_id/table_columns/close.
2. إضافة `backend/erp/db_adapters/sqlite.py` لتنفيذ العقد فوق sqlite3، مع foreign keys وbusy timeout وWAL والـrow mapping الحالي.
3. ربط الدوال المركزية في `erp/db.py` بتنفيذ SQLite adapter بدل إنشاء الاتصال وتنفيذ العمليات المركزية مباشرةً في ذلك الملف.
4. الإبقاء على SQLite هو المحرك الوحيد. لا يوجد اختيار DATABASE_URL أو PostgreSQL driver، ولا يمكن اعتبار هذا التغيير دعمًا لمحرك آخر.
5. إضافة اختبارات عقد adapter وجرد PostgreSQL، وإضافة تقرير preflight معلوماتي إلى GitHub Actions كـartifact.

## حدود العقد الحالية

- `migrations`, `schema.sql`, `seed`, `backup_bundle`, `api/admin` وبعض إدارة الأخطاء ما زالت SQLite-specific.
- بعض الخدمات والمهاجرات تتعامل مباشرة مع connection/cursor؛ لذلك لا يمكن تبديل adapter في runtime إلى PostgreSQL الآن.
- `executescript`, SQLite online backup, PRAGMA integrity/foreign_key check, lastrowid وBEGIN IMMEDIATE لا تملك بديلًا عامًا حرفيًا.
- لا يوجد PostgresAdapter أو psycopg/SQLAlchemy اعتماد في requirements.

## قرار التصميم

Stage 20 يثبت seam وعقد SQLite أولًا، لكنه لا يحسم بعد psycopg 3 مقابل SQLAlchemy Core. لا نضيف اعتمادًا أو نعيد كتابة qmark قبل بناء prototype واختباره على SQL الموجود. يلزم تقييم:

- معاملات named/positional بأمان دون تحويل نصي غير واعٍ بقواعد SQL.
- `INSERT ... RETURNING id` بدل lastrowid.
- row mapping، exceptions/SQLSTATE، nested savepoints، transaction isolation.
- introspection/migrations وbackup/restore.
- tenant context transaction-local مع connection pool.

الخياران يبقيان مفتوحين. التوصية القادمة: نموذج مقارنة صغير على واجهات الدوال نفسها، مع سيناريوهات معاملات وconstraints وعمليات متعددة المستأجرين، ثم اختيار driver على أدلة تنفيذية.

## تحقق Stage 20

- 10 اختبارات SQLite adapter contract.
- 6 اختبارات PostgreSQL compatibility preflight.
- 155 اختبار عزل tenant السابق.
- لم تنفذ اختبارات HTTP/Flask أو PostgreSQL أو RLS أو pool reuse في هذه البيئة.
