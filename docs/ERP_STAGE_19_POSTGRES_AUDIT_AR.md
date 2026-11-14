# ERP Global Pro — Stage 19: تدقيق توافق PostgreSQL

**التصنيف:** جرد ساكن لمصدر المشروع، وليس اختبار اتصال أو تشغيل PostgreSQL.
**نقطة المصدر:** Stage 18، مخطط SQLite v8؛ أعيد تضمين أداة الفحص في حزمة Stage 20.

## الخلاصة

المشروع لا يستطيع الانتقال إلى PostgreSQL بمجرد تغيير متغير اتصال. طبقة البيانات والمخطط والترحيلات وإجراءات النسخ الاحتياطي والإدارة تستخدم خصائص SQLite مباشرة. يلزم Adapter حقيقي، ومجموعة migrations مستقلة، ومفاتيح مركبة وRLS، ثم اختبارات اتصال حقيقية.

## الجرد الحالي بعد إضافة نقطة Adapter في Stage 20

- 49 ملفًا تشغيليًا من Python/SQL/CLI ضمن نطاق الفحص.
- مخطط SQLite الحالي: v8.
- 865 علامة `?` خام داخل ملفات Python؛ هذا عدّ أحرف فقط، وليس عدد معاملات DB-API المؤكد.
- 45 موضع PRAGMA، و42 AUTOINCREMENT، و22 sqlite_master، و53 مرجع sqlite3، و10 CREATE TRIGGER، و3 lastrowid، و3 BEGIN IMMEDIATE، و3 INSERT OR IGNORE/REPLACE، و4 row_factory، و4 executescript، وموضع واحد لأخطاء SQLite المحددة. قد تتداخل فئات العد؛ لا تجمعها باعتبارها مواضع فريدة.
- اختبارات PostgreSQL فعلية: صفر.

الأداة:

```bash
cd backend
python scripts/postgres_compat_preflight.py --output artifacts/postgres-compat-preflight.json
```

التقرير informational ولا يعد بوابة توافق PostgreSQL. جرد `?` لا يبرر أي استبدال نصي جماعي؛ قد توجد علامات داخل نصوص أو تعليقات أو سياقات غير معاملات.

## عوائق النقل الأساسية

1. `erp/db.py` وملف SQLite adapter: الاتصال، المعاملات، row mapping، BEGIN IMMEDIATE، introspection، وآخر insert ID.
2. `erp/migrations/__init__.py`: `sqlite_master` وPRAGMA وإعادة بناء الجداول والمشغلات.
3. `schema.sql`: AUTOINCREMENT وNOCASE ومشغلات SQLite.
4. seed: INSERT OR IGNORE.
5. الخدمات وواجهات API: SQL مباشر وعلامات qmark.
6. `errors.py`: معالجات استثناءات SQLite.
7. backup/restore وعمليات admin: SQLite online backup وPRAGMA integrity/FK checks.

## قرار العزل المطلوب في PostgreSQL

- `company_id NOT NULL` في كل جدول مملوك للشركة.
- composite foreign keys لكل العلاقات الحساسة.
- RLS مع `USING` و`WITH CHECK` على جميع جداول المستأجرين.
- تمرير الشركة عبر سياق خادم موثق باستخدام `SET LOCAL` داخل transaction، لا `SET` دائم على اتصال pool.
- اختبار إعادة استخدام الاتصال: طلب A ثم B على الاتصال نفسه دون تسرب سياق.
- مسار backup/restore خاص بـPostgreSQL ومتسق مع مخزن الملفات.

**الحالة:** PostgreSQL migration لم تبدأ. لا يوجد driver مثبت أو خادم PostgreSQL أو RLS. لا تغيّر `TENANT_DATA_SCOPING_ENABLED=False`.
