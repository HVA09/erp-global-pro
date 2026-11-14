# ERP Global Pro — Stage 8: مراجعة Callbacks والاستدعاءات الجانبية

**التاريخ:** 13 نوفمبر 2026
**نوع العمل:** مراجعة ساكنة للقراءة فقط بعد تنفيذ Stage 7.
**الملفات المحورية:** `backend/erp/resources.py`, `services/inventory.py`, `services/settings.py`, `services/accounting.py`.

## النتيجة التنفيذية

أصبح محرك CRUD المشترك tenant-aware، لكن callbacks والاستعلامات المشتقة والخدمات التي تنفذها ما زالت تحتوي مسارات SQL غير مقيدة صراحةً بـ `company_id`. لذلك لا يمكن اعتبار Stage 7 كافية لتفعيل تعدد الشركات. Stage 8 لم تغيّر كود التشغيل؛ هدفها تحديد سلسلة الاستدعاء ذات التأثير المالي وترتيب معالجتها قبل السماح بالكتابة من أكثر من شركة.

## 1. سجل callbacks المعرفة في `resources.py`

| المورد | callback | نوع السلوك | نتيجة الفحص |
|---|---|---|---|
| customers / suppliers | `_partner_before_delete` | فحص قيمة `is_system` في الصف | لا ينفذ SQL مستقلًا؛ الصف نفسه جُلب بنطاق الشركة في Stage 7 |
| products | `_product_before_save` | قراءة `stock_levels` حسب `product_id` | استعلام لا يتضمن `company_id` صراحة؛ يحتاج تمرير الشركة أو الاعتماد على RLS لاحقًا |
| products | `_product_after_save` | إنشاء رصيد افتتاحي | يستدعي سلسلة مخزون ومحاسبة وإعدادات غير محوّلة بعد؛ أعلى خطر |
| accounts | `_account_before_save` | قراءة `journal_lines` حسب `account_id` | استعلام لا يتضمن `company_id` صراحة |
| accounts | `_account_before_delete` | فحص `is_system` في الصف | لا ينفذ SQL مستقلًا |
| leave_requests | `_leave_before_save` | مقارنة start/end date | تحقق محلي بلا SQL |
| leads | `_lead_before_save` | منع ضبط status إلى converted مباشرة | تحقق محلي بلا SQL |
| opportunities | `_opportunity_before_save` | ضبط probability حسب stage | تعديل بيانات داخلية محلي فقط |
| maintenance_requests | `_request_before_save` | حساب reported/resolved dates | يستخدم التاريخ فقط ولا ينفذ SQL مستقلًا |

## 2. سلسلة إنشاء منتج برصيد افتتاحي — P0

`_product_after_save()` يستدعي:

1. `settings.default_warehouse_id()` لاختيار المستودع الافتراضي.
2. `inventory.opening_stock()` ثم `inventory.adjust()`.
3. `inventory._require_stock_product()` و`get_product()` و`get_warehouse()` و`level()`.
4. `settings.check_period_open()` و`settings.get_settings()`.
5. `receive()` و`_insert_move()` و`_apply_level()` لكتابة `stock_moves` و`stock_levels` وتحديث تكلفة المنتج.
6. `accounting.post_entry()` لتسجيل قيد المحاسبة المرتبط بحركة المخزون.

### الملاحظات

- الاستعلامات في `inventory.py` تستعمل في مواضع عدة `id` أو `product_id` أو `warehouse_id` من دون `company_id` صريح.
- دوال الإدخال إلى `stock_moves` و`stock_levels` لا تستقبل `company_id` كوسيط واضح؛ وقد تعتمد على DEFAULT الشركة الأساسية في مخطط SQLite الحالي.
- `settings.get_settings()` يقرأ كل الصفوف باستخدام `SELECT key,value FROM settings`، و`save_settings()` يستخدم المفتاح العالمي `key`، بينما `default_warehouse_id()` يبحث في المستودعات دون نطاق الشركة.
- `accounting.post_entry()` يشارك في أثر المخزون المالي، ويجب أن يُمرر إليه سياق شركة موثوق ويُقيد كل حساب/فترة/قيد/سطر بالشركة نفسها.
- لا يكفي قيد المنتج الأصلي في CRUD، لأن callback ينتج عدة كتابات جانبية يجب أن تكون ضمن نفس معاملة الشركة نفسها.

## 3. استعلامات مشتقة في تعريفات الموارد

هناك استعلامات SELECT فرعية في موارد:

- customers/suppliers: رصيد الشريك من `trade_documents` عبر `partner_id`.
- products: إجمالي الكمية والقيمة من `stock_levels` عبر `product_id`.
- accounts: صافي الحساب من `journal_lines` عبر `account_id`.
- projects: ساعات الوقت والتكلفة وعدد المهام عبر `time_logs` و`tasks` و`employees`.

ترابط المراجع في Stage 4 يمنع إنشاء علاقة مباشرة بين شركتين في SQLite، لكن هذه الاستعلامات لا تحتوي على شروط tenant صريحة. يجب إضافة نطاق الشركة في SQL أو ضمان RLS في PostgreSQL واختبارها؛ لا تعتبر مشغلات v4 بديلًا كاملًا عن نطاق القراءة.

## 4. خطة إغلاق المخاطر

### P0 — المخزون والمحاسبة

- أضف `company_id` مطلوبًا إلى واجهات خدمات المخزون، ومرّره صراحةً من سياق الشركة الموثق أو من job context مصادق عليه.
- قيّد `get_product`, `get_warehouse`, `level`, `allocate_lots`, `_require_stock_product`, `receive`, `issue_detail`, `adjust`, `transfer`, `create_count`, `low_stock`.
- اكتب `company_id` دائمًا في `stock_moves` و`stock_levels` و`stock_counts` و`stock_count_lines`، ولا تعتمد على DEFAULT الشركة الأساسية.
- اجعل تحديث تكلفة المنتج واستدعاءات حساب الأرصدة مقيدة بالشركة.
- أضف company scope إلى `accounting.post_entry` وكل اختيار حساب/فترة/دفتر/قيد/سطر.
- اختبر إنشاء منتج برصيد افتتاحي للشركة A مع وجود سجلات مماثلة للشركة B، وتحقق من عدم إنشاء حركة أو قيد في الشركة الأخرى.

### P0 — Settings وSequences

- لا تستخدم `settings.key` كمعرّف عالمي لقيم الشركات. صمم مفتاحًا مركبًا أو جدول إعدادات جديدًا مع `(company_id, key)`، بعد خطة ترحيل قابلة للرجوع.
- صمم `sequences` بمفتاح `(company_id, name)`، واختبر أن أرقام الفواتير والتسلسلات مستقلة بين الشركات ولا تتكرر داخل الشركة.

### P1 — الاستعلامات المشتقة والموارد

- أضف `company_id` إلى كل subquery / aggregate ذات صلة، ولا تعتمد فقط على كون IDs عالمية.
- مرر company_id إلى callbacks أو غيّر العقد بحيث لا تستدعي خدماتًا تعتمد على Flask `g` فقط؛ jobs والاختبارات تحتاج سياقًا صريحًا.
- تأكد أن callback side effects تشارك نفس transaction ولا تترك حركة مخزون دون قيد أو قيدًا دون حركة.

## 5. الاختبارات المطلوبة قبل إغلاق Stage 8

- اختبار طرفين للشركات A/B: منتجان ومستودعان وحساب مخزون لكل شركة.
- إنشاء منتج برصيد افتتاحي داخل A؛ افحص `stock_moves`, `stock_levels`, `products.cost_cents`, `journal_entries`, `journal_lines` لكل شركة.
- فشل العملية في منتصف السلسلة يجب أن يعيد كل تغييرات المخزون والمحاسبة إلى نقطة البداية.
- يمنع تغيير default warehouse للشركة A أن يؤثر في الشركة B.
- تسلسلات المستندات تُحسب داخل الشركة فقط.
- تقارير الرصيد ومجاميع المخزون لا تجمع بيانات الشركتين.
- تشغيل اختبارات Flask حقيقية ومسارات API، لا محولات اختبارية فقط.

## قرار الإطلاق

- لم تتغير أي دالة تشغيلية في Stage 8؛ هذا تقرير مراجعة ساكنة فقط.
- يجب أن يبقى `TENANT_DATA_SCOPING_ENABLED=False`.
- لا تستخدم أكثر من شركة تشغيلية، ولا تنشر الحزمة على أنها نظام متعدد الشركات جاهز.
