# ERP Global Pro — سجل تغييرات Stage 10

**التاريخ:** 13 نوفمبر 2026
**المرحلة:** مفاتيح التسلسلات والتفرد التجاري حسب الشركة
**SQLite schema version:** 6
**الحالة:** نقطة تطوير واختبار؛ ليست موافقة تشغيل متعدد الشركات في الإنتاج.

## 1. الهدف

إنهاء مفاتيح التفرد التجارية التي كانت عالمية في SQLite حتى يصبح نطاقها داخل الشركة، مع الاحتفاظ بالمفاتيح العالمية لهوية الدخول وكتالوج الأدوار. تم تضمين فحص مسبق داخل الترحيل يرفض الملكية الناقصة أو أي تكرار ضمن الشركة نفسها، ولا يصلح البيانات تلقائيًا.

## 2. الترحيل v6

- ارتفع `LATEST_VERSION` إلى `6`.
- أعيد بناء `sequences` ليصبح مفتاحه الأساسي `(company_id, name)` بدل `name` فقط.
- تم تعديل خدمة `sequences.next_number()` لتقرأ وتحدّث وتزيد الرقم ضمن `(company_id, name)`، وتسمح بوجود تسلسل باسم واحد لكل شركة دون مشاركة العدّاد أو البادئة.
- أعيد بناء الجداول التي كانت تحمل قيود `UNIQUE` داخل تعريف الجدول: `accounts`, `attendance`, `categories`, `cost_centers`, `departments`, `depreciation_postings`, `journal_entries`, `products`, `stock_levels`, `warehouses`.
- أصبح المفتاح الأساسي لـ `stock_levels` هو `(company_id, product_id, warehouse_id)`.
- استبدلت الفهارس الجزئية العالمية بفهرس شركة مركب مع الإبقاء على نفس predicates: `products.barcode IS NOT NULL`, `trade_documents.number IS NOT NULL`, و`payroll_runs.status = 'posted'`.
- لم يُعد بناء `settings` لأن مفتاحها `(company_id,key)` نُفّذ في Stage 9؛ تحقّق Stage 10 من ذلك صراحةً.

## 3. قواعد التفرد الـ15

أصبحت قواعد الأعمال الـ15 الخاصة بالشركة كما يلي:

1. `accounts(company_id, code)`
2. `attendance(company_id, employee_id, date)`
3. `categories(company_id, name)`
4. `cost_centers(company_id, name)`
5. `departments(company_id, name)`
6. `depreciation_postings(company_id, asset_id, period)`
7. `journal_entries(company_id, number)`
8. `payroll_runs(company_id, month)` حيث `status='posted'`
9. `products(company_id, sku)`
10. `products(company_id, barcode)` حيث `barcode IS NOT NULL`
11. `sequences(company_id, name)` — مفتاح أساسي مركب
12. `settings(company_id, key)` — مفتاح أساسي مركب منذ Stage 9
13. `stock_levels(company_id, product_id, warehouse_id)` — مفتاح أساسي مركب
14. `trade_documents(company_id, doc_type, number)` حيث `number IS NOT NULL`
15. `warehouses(company_id, code)`

هوية `users.email` واسم الدور المشترك `roles.name` يظلان عالميين عمدًا. كما تظل مفاتيح الصفوف الرقمية surrogate IDs عامة، وليست نطاقات أكواد أعمال.

## 4. أمان الترحيل والمحافظة على البيانات

- فحص v6 الداخلي يطلب وجود الجداول والأعمدة المطلوبة، ويرفض أي صف بلا `company_id`، ويرفض تكرارات المفاتيح داخل الشركة قبل بناء القيود.
- أثناء إعادة بناء جداول SQLite، تحفظ الترحيلات جميع مشغلات قاعدة البيانات، وتزيلها مؤقتًا داخل معاملة الترحيل، ثم تعيدها بعد اكتمال إعادة التسمية والفهارس. هذا يشمل مشغلات العلاقات العابرة للجداول، لا مشغلات الجدول الجاري فقط.
- تُقارن أعداد الصفوف قبل/بعد نسخ كل جدول معاد بناؤه، وكذلك جدول التسلسلات.
- يعاد فحص جميع المفاتيح الأجنبية عبر `PRAGMA foreign_key_check`، ويتم التحقق من استعادة عدد المشغلات.
- إذا فشل أي فحص، تتراجع معاملة الترحيل كاملة. لا توجد عملية تنظيف تلقائي للأكواد أو الأرقام المتكررة.

## 5. الاختبارات

أضيف `backend/tests/test_tenant_stage10_uniqueness_migration.py` بأربعة اختبارات: مخطط وترحيل v6، تكرار المفاتيح بين شركتين، رفض التكرار داخل الشركة نفسها، واستقلال عدادات التسلسل بين الشركتين باستخدام خدمة التسلسلات نفسها.

كما حُدّث اختبار Stage 9 لخدمة التسلسلات ليعكس سلوك v6 الجديد بدل توقع الرفض القديم `tenant_sequence_migration_pending`.

## 6. ما لم يكتمل

- لم يقدم المستخدم ملف قاعدة بيانات إنتاج، ولذلك لم يُنفّذ تقرير preflight على بيانات حقيقية. فحص v6 نُفّذ على قواعد اختبار SQLite كاملة مبنية من `schema.sql` مع نسخ تجريبية.
- اختبارات Flask/HTTP الكاملة لم تُشغّل؛ Flask غير متوفر في بيئة التنفيذ الحالية.
- PostgreSQL وcomposite foreign keys وRLS لم تُنفذ.
- لم تكتمل خدمات وواجهات Trade/Payments وHR/Payroll وManufacturing وبقية APIs والتقارير والاستيراد/التصدير والملفات والمهام الخلفية.
- تبقى حواجز منع الكتابة عند وجود أكثر من شركة كما هي، و`TENANT_DATA_SCOPING_ENABLED=False`.

## 7. قرار التشغيل

Stage 10 تهيئ قيود التفرد والتسلسل على مستوى المخطط فقط. لا تعني أن تدفق المستندات والمدفوعات والموارد البشرية كامل العزل. لا تنشئ شركات تشغيلية متعددة ولا تفعّل بوابة العزل. قبل تطبيق migration v6 على أي قاعدة فعلية: خذ backup، استعد نسخة مستقلة، شغّل preflight، راجع التقرير، ثم جرّب الترحيل على النسخة المستعادة أولاً. لا توجد migration downgrade؛ التراجع بعد نجاح التطبيق يكون باستعادة النسخة الاحتياطية السابقة.

**نقطة المتابعة التالية:** Stage 11 — Trade/Payments services وAPIs، بما يشمل المستندات وخطوطها والمدفوعات والتطبيقات والإلغاء والعكس وسلاسل الترقيم.
